import os
import numpy as np
import json
import cv2
import scipy.io as sio
from scipy import interpolate
import transformations
import datareader

limbs = [[0,1],[2,3],[3,4],[5,6],[6,7],[8,9],[9,10],[11,12],[12,13],[2,5,8,11]]	

def getPersonScale(joints):
	torso_size = (-joints[0][1] + (joints[8][1] + joints[11][1])/2.0)
	peak_to_peak = np.ptp(joints,axis=0)[1]
	#rarm_length = np.sqrt((joints[2][0] - joints[4][0])**2 + (joints[2][1]-joints[4][1])**2)			
	#larm_length = np.sqrt((joints[5][0] - joints[7][0])**2 + (joints[5][1]-joints[7][1])**2)
	rcalf_size = np.sqrt((joints[9][1] - joints[10][1])**2 + (joints[9][0] - joints[10][0])**2)
	lcalf_size = np.sqrt((joints[12][1] - joints[13][1])**2 + (joints[12][0] - joints[13][0])**2)
	calf_size = (lcalf_size + rcalf_size)/2.0

	size = np.max([2.5 * torso_size,calf_size*5,peak_to_peak*1.1]) 
	return (size/200.0)

def getExampleInfo(vid_name,frame_num,box,X):
	I_name_j = os.path.join(vid_name,str(frame_num+1)+'.jpg')

	if(not os.path.isfile(I_name_j)):
		I_name_j = os.path.join(vid_name,str(frame_num+1)+'.png')

	joints = X[:,:,frame_num]-1.0
	box_j = box[frame_num,:]
	scale = getPersonScale(joints)
	pos = [(box_j[0] + box_j[2]/2.0), (box_j[1] + box_j[3]/2.0)] 
	lj = [I_name_j] + np.ndarray.tolist(joints.flatten()) + pos + [scale]
	return lj

def readExampleInfo(example):
	I = cv2.imread(example[0])
	joints = np.reshape(np.array(example[1:29]), (14,2))
	scale = example[31]
	pos = np.array(example[29:31])
	return (I,joints,scale,pos)


def warpExampleGenerator(vid_info_list,param,do_augment=True,return_pose_vectors=False,return_class=False):
    
	img_width = param['IMG_WIDTH']
	img_height = param['IMG_HEIGHT']
	pose_dn = param['posemap_downsample']
	sigma_joint = param['sigma_joint']
	n_joints = param['n_joints']
	scale_factor = param['obj_scale_factor']	
	batch_size = param['batch_size']

	while True:

		X_src = np.zeros((batch_size,img_height,img_width,3))
		X_mask_src = np.zeros((batch_size,img_height,img_width,len(limbs)+1))
		X_pose_src = np.zeros((batch_size,img_height/pose_dn,img_width/pose_dn,14))
		X_pose_tgt = np.zeros((batch_size,img_height/pose_dn,img_width/pose_dn,14))
		X_trans = np.zeros((batch_size,2,3,11))
		X_posevec_src = np.zeros((batch_size,n_joints*2))
		X_posevec_tgt = np.zeros((batch_size,n_joints*2))
		#X_class = np.zeros((batch_size,1))
		Y = np.zeros((batch_size,img_height,img_width,3))
	
		for i in xrange(batch_size):
				
			#1. choose random video.
			vid = np.random.choice(len(vid_info_list),1)[0]		
			vid_info = vid_info_list[vid][0]
			vid_bbox = vid_info_list[vid][1]
			vid_X = vid_info_list[vid][2]
			vid_path = vid_info_list[vid][3]	
			#vid_class = vid_info_list[vid][4]			

			#2. choose pair of frames
			n_frames = vid_X.shape[2]	
			frames = np.random.choice(n_frames,2,replace=False)
			while(abs(frames[0] - frames[1])/(n_frames*1.0) <= 0.02):
				frames = np.random.choice(n_frames,2,replace=False)

			example0 = getExampleInfo(vid_path,frames[0],vid_bbox,vid_X)
			example1 = getExampleInfo(vid_path,frames[1],vid_bbox,vid_X)
			
			I0,joints0,scale0,pos0 = readExampleInfo(example0)
			I1,joints1,scale1,pos1 = readExampleInfo(example1)
			#X_class[i,0] = vid_class

			#pos = pos0		
			#scale=scale_factor/scale0
			if(scale0 > scale1):
				pos = pos0
				scale = scale_factor/scale0
			else:
				pos = pos1
				scale = scale_factor/scale1	

			I0,joints0 = centerAndScaleImage(I0,img_width,img_height,pos,scale,joints0)
			I1,joints1 = centerAndScaleImage(I1,img_width,img_height,pos,scale,joints1)

			I0 = (I0/255.0 - 0.5)*2.0
			I1 = (I1/255.0 - 0.5)*2.0

			if(do_augment):
				rflip,rscale,rshift,rdegree,rsat = randAugmentations(param)				
				#rshift2 = randShift(param)
				#rshift0 = (rshift[0] + rshift2[0], rshift[1]+rshift2[1])
				I0,joints0 = augment(I0,joints0,rflip,rscale,rshift,rdegree,rsat,img_height,img_width)	
				I1,joints1 = augment(I1,joints1,rflip,rscale,rshift,rdegree,rsat,img_height,img_width)	

			posemap0 = makeJointHeatmaps(img_height,img_width,joints0,sigma_joint,pose_dn)
			posemap1 = makeJointHeatmaps(img_height,img_width,joints1,sigma_joint,pose_dn)

			src_limb_masks = makeLimbMasks(joints0,img_width,img_height)	
			src_bg_mask = np.expand_dims(1.0 - np.amax(src_limb_masks,axis=2),2)
			src_masks = np.log(np.concatenate((src_bg_mask,src_limb_masks),axis=2)+1e-10)

			X_src[i,:,:,:] = I0
			X_pose_src[i,:,:,:] = posemap0
			X_pose_tgt[i,:,:,:] = posemap1
			X_mask_src[i,:,:,:] = src_masks
			X_trans[i,:,:,0] = np.array([[1.0,0.0,0.0],[0.0,1.0,0.0]])
			X_trans[i,:,:,1:] = getLimbTransforms(joints0,joints1)

			X_posevec_src[i,:] = joints0.flatten()
			X_posevec_tgt[i,:] = joints1.flatten()

			Y[i,:,:,:] = I1
		
		out = [X_src,X_pose_src,X_pose_tgt,X_mask_src,X_trans]
		
		if(return_pose_vectors):
			out.append(X_posevec_src)
			out.append(X_posevec_tgt)		
		if(return_class):
			out.append(X_class)

		yield (out,Y)


def createFeed(params,vid_file,do_augment=True,return_pose_vectors=False,return_class=False,transfer=False):
	#ex_list = datareader.makeWarpExampleList(ex_file,n_examples)
	vid_info_list = datareader.makeVidInfoList(vid_file)
	
	if(transfer):
		feed = transferExampleGenerator(ex_list,ex_list,params)
	else:
		feed = warpExampleGenerator(vid_info_list,params,do_augment,return_pose_vectors,return_class)

	return feed


def transferExampleGenerator(examples0,examples1,param):
    
	img_width = param['IMG_WIDTH']
	img_height = param['IMG_HEIGHT']
	pose_dn = param['posemap_downsample']
	sigma_joint = param['sigma_joint']
	n_joints = param['n_joints']
	scale_factor = param['obj_scale_factor']	
	batch_size = param['batch_size']

	while True:

		for i in xrange(batch_size):
			X_src = np.zeros((batch_size,img_height,img_width,3))
			X_mask_src = np.zeros((batch_size,img_height,img_width,len(limbs)+1))
			X_pose_src = np.zeros((batch_size,img_height/pose_dn,img_width/pose_dn,n_joints))
			X_pose_tgt = np.zeros((batch_size,img_height/pose_dn,img_width/pose_dn,n_joints))
			X_trans = np.zeros((batch_size,2,3,11))	
			Y = np.zeros((batch_size,img_height,img_width,3))

			for i in xrange(batch_size):
				example0 = examples0[np.random.randint(0,len(examples0))] 
				example1 = examples1[np.random.randint(0,len(examples1))] 
				while(example0[-1] == example1[-1]):
					example1 = examples1[np.random.randint(0,len(examples1))] 

				I0,joints0,scale0,pos0 = readExampleInfo(example0[:32])
				I1,joints1,scale1,pos1 = readExampleInfo(example1[32:64])

				scale0 = scale_factor/example0[31]
				scale1 = scale_factor/example1[31]

				pos0 = np.array(example0[29:31])
				I0,joints0 = centerAndScaleImage(I0,img_width,img_height,pos0,scale0,joints0)

				I1 = cv2.resize(I1,(0,0),fx=scale1,fy=scale1)
				joints1 = joints1 * scale1
				offset = (joints0[10,:]+joints0[13,:] - joints1[10,:] - joints1[13,:])/2.0
				joints1 += np.tile(offset,(14,1)) 
				T = np.float32([[1,0,offset[0]],[0,1,offset[1]]])	
				I1 = cv2.warpAffine(I1,T,(img_width,img_height))

				I0 = (I0/255.0 - 0.5)*2.0
				I1 = (I1/255.0 - 0.5)*2.0

				posemap0 = makeJointHeatmaps(img_height,img_width,joints0,sigma_joint,pose_dn)
				posemap1 = makeJointHeatmaps(img_height,img_width,joints1,sigma_joint,pose_dn)

				src_limb_masks = makeLimbMasks(joints0,img_width,img_height)	
				src_bg_mask = np.expand_dims(1.0 - np.amax(src_limb_masks,axis=2),2)
				src_masks = np.log(np.concatenate((src_bg_mask,src_limb_masks),axis=2)+1e-10)

				X_src[i,:,:,:] = I0
				X_pose_src[i,:,:,:] = posemap0
				X_pose_tgt[i,:,:,:] = posemap1
				X_mask_src[i,:,:,:] = src_masks
				X_trans[i,:,:,0] = np.array([[1.0,0.0,0.0],[0.0,1.0,0.0]])
				X_trans[i,:,:,1:] = getLimbTransforms(joints0,joints1)
				Y[i,:,:,:] = I1
	
			yield ([X_src,X_pose_src,X_pose_tgt,X_mask_src,X_trans],Y)



'''
def actionExampleGenerator(examples,param,return_pose_vectors=False):
    
	img_width = param['IMG_WIDTH']
	img_height = param['IMG_HEIGHT']
	pose_dn = param['posemap_downsample']
	sigma_joint = param['sigma_joint']
	n_joints = param['n_joints']
	scale_factor = param['obj_scale_factor']	

	while True:

		I0,joints0,scale0,pos0 = readExampleInfo(examples[0])
		scale = scale_factor/scale0
		I0,joints0 = centerAndScaleImage(I0,img_width,img_height,pos0,scale,joints0)
		posemap0 = makeJointHeatmaps(img_height,img_width,joints0,sigma_joint,pose_dn)
		
		src_limb_masks = makeLimbMasks(joints0,img_width,img_height)	
		bg_mask = np.expand_dims(1.0 - np.amax(src_limb_masks,axis=2),2)
		src_masks = np.log(np.concatenate((bg_mask,src_limb_masks),axis=2)+1e-10)
	
		for i in xrange(1,len(examples)):	
			I1,joints1,scale1,pos1 = readExampleInfo(examples[i])
			I1,joints1 = centerAndScaleImage(I1,img_width,img_height,pos0,scale,joints1)
			posemap1 = makeJointHeatmaps(img_height,img_width,joints1,sigma_joint,pose_dn)
	
			X_src = np.expand_dims(I0,0)
			X_pose_src = np.expand_dims(posemap0,0)
			X_pose_tgt = np.expand_dims(posemap1,0)
			X_mask_src = np.expand_dims(src_masks,0)
			X_trans = np.zeros((1,2,3,11))
			X_trans[i,:,:,0] = np.array([[1.0,0.0,0.0],[0.0,1.0,0.0]])
			X_trans[i,:,:,1:] = getLimbTransforms(joints0,joints1)
			X_posevec_src = np.expand_dims(joints0.flatten(),0)
			X_posevec_tgt = np.expand_dims(joints1.flatten(),0)

			Y[i,:,:,:] = I1
	
			if(not return_pose_vectors):
				yield ([X_src,X_pose_src,X_pose_tgt,X_mask_src,X_trans],Y)
			else:
				yield ([X_src,X_pose_src,X_pose_tgt,X_mask_src,X_trans,X_posevec_src,X_posevec_tgt],Y)
'''

'''
def poseExampleGenerator(examples,param):
    
	img_width = param['IMG_WIDTH']
	img_height = param['IMG_HEIGHT']
	pose_dn = param['posemap_downsample']
	sigma_joint = param['sigma_joint']
	n_joints = param['n_joints']
	scale_factor = param['obj_scale_factor']	
	batch_size = param['batch_size']

	while True:

		X_src = np.zeros((batch_size,img_height,img_width,3))
		X_mask = np.zeros((batch_size,img_height/pose_dn,img_width/pose_dn,1))
		Y = np.zeros((batch_size,img_height/pose_dn,img_width/pose_dn,n_joints))

		for i in xrange(batch_size):
			example = examples[np.random.randint(0,len(examples))]	
			I = readImage(example[0])

			joints = np.reshape(np.array(example[1:29]), (14,2))
			pos = np.array(example[29:31])	
			scale = 1.15/(example[31])

			I,joints = centerAndScaleImage(I,img_width,img_height,pos,scale,joints)

			rscale,rshift,rdegree,rsat = randAugmentations(param)
	
			I,joints = augment(I,joints,rscale,rshift,rdegree,rsat,img_height,img_width)	

			posemap = makeJointHeatmaps(img_height,img_width,joints,sigma_joint,pose_dn)
		
			fg_mask = makeGaussianMap(img_width/pose_dn,img_height/pose_dn,
									np.array([img_width/(2.0*pose_dn),img_height/(2.0*pose_dn)]),
									   21.0**2,21**2,0.0)
			
			X_src[i,:,:,:] = I
			X_mask[i,:,:,0] = fg_mask
			
			Y[i,:,:,:] = posemap

		yield ([X_src,X_mask],Y)

def drawLimbsOnImage(I,joints,color=(0,0,255)):	

	n_limbs = len(limbs)

	#if(color is None):
	#	colors = [(0,0,0),(255,0,0),(0,255,0),(255,0,0),(0,255,0),(0,0,255), (255,255,0),(0,0,255),(255,255,0)]

	for i in xrange(n_limbs):	
		n_joints_for_limb = len(limbs[i])
		if(n_joints_for_limb != 2):
			continue

		p1 = (int(joints[limbs[i][0],0]),int(joints[limbs[i][0],1]))
		p2 = (int(joints[limbs[i][1],0]),int(joints[limbs[i][1],1]))
		cv2.line(I,p1,p2,color,2) #colors[i],2)
	
	return I
'''

def randScale(param):
	rnd = np.random.rand()
	return ( param['scale_max']-param['scale_min']) * rnd + param['scale_min']

def randRot( param ):
	return (np.random.rand()-0.5)*2 * param['max_rotate_degree']

def randShift( param ):
	shift_px = param['max_px_shift']
	x_shift = int(shift_px * (np.random.rand()-0.5))
	y_shift = int(shift_px * (np.random.rand()-0.5))
	return x_shift, y_shift

def randSat(param):
	min_sat = 1 - param['max_sat_factor']
	max_sat = 1 + param['max_sat_factor']

	return np.random.rand()*(max_sat-min_sat) + min_sat


def randAugmentations(param):

	rflip = np.random.rand()
	rscale = randScale(param)
	rshift = randShift(param)
	rdegree = randRot(param)
	rsat = randSat(param)
	
	return rflip,rscale,rshift,rdegree,rsat


def augment(I,joints,rflip,rscale,rshift,rdegree,rsat,img_height,img_width):
	I,joints = augFlip(I,rflip,joints)
	I,joints = augScale(I,rscale,joints)
	I,joints = augShift(I,img_width,img_height,rshift,joints)
	I,joints = augRotate(I,img_width,img_height,rdegree,joints)
	I = augSaturation(I,rsat)

	return I,joints


def centerAndScaleImage(I,img_width,img_height,pos,scale,joints):

	I = cv2.resize(I,(0,0),fx=scale,fy=scale)
	joints = joints * scale

	x_offset = (img_width-1.0)/2.0 - pos[0]*scale
	y_offset = (img_height-1.0)/2.0 - pos[1]*scale

	T = np.float32([[1,0,x_offset],[0,1,y_offset]])	
	I = cv2.warpAffine(I,T,(img_width,img_height))

	joints[:,0] += x_offset
	joints[:,1] += y_offset

	return I,joints

def augJointShift(joints,max_joint_shift):
	joints += (np.random.rand(joints.shape) * 2 - 1) * max_joint_shift
	return joints

def augFlip(I,rflip,joints):
	
	if(rflip < 0.5):
		return I,joints
	
	I = np.fliplr(I)
	joints[:,0] = I.shape[1] - 1 - joints[:,0]

	right = [2,3,4,8,9,10]
	left = [5,6,7,11,12,13]

	for i in xrange(6):
		tmp = np.copy(joints[right[i],:])
		joints[right[i],:] = np.copy(joints[left[i],:])
		joints[left[i],:] = tmp
	
	return I,joints

def augScale(I,scale_rand, joints):
	I = cv2.resize(I,(0,0),fx=scale_rand,fy=scale_rand)
	joints = joints * scale_rand
	return I,joints


def augRotate(I,img_width,img_height,degree_rand,joints):
	h = I.shape[0]
	w = I.shape[1]	

	center = ( (w-1.0)/2.0, (h-1.0)/2.0 )
	R = cv2.getRotationMatrix2D(center,degree_rand,1)	
	I = cv2.warpAffine(I,R,(img_width,img_height))

	for i in xrange(joints.shape[0]):
		joints[i,:] = rotatePoint(joints[i,:],R)

	return I,joints

def rotatePoint(p,R):
	x_new = R[0,0] * p[0] + R[0,1] * p[1] + R[0,2]
	y_new = R[1,0] * p[0] + R[1,1] * p[1] + R[1,2]	
	return np.array((x_new,y_new))


def augShift(I,img_width,img_height,rand_shift,joints):
	x_shift = rand_shift[0]
	y_shift = rand_shift[1]

	T = np.float32([[1,0,x_shift],[0,1,y_shift]])	
	I = cv2.warpAffine(I,T,(img_width,img_height))

	joints[:,0] += x_shift
	joints[:,1] += y_shift

	return I,joints

def augSaturation(I,rsat):
	I  *= rsat
	I[I > 1] = 1
	return I

'''
def makeVelocityFields(height,width,transforms):

	xv,yv = np.meshgrid(np.array(range(width)),np.array(range(height)),sparse=False,indexing='xy')
	p = np.vstack((xv.flatten(),yv.flatten(),np.ones(height*width)))
	V = np.zeros((height,width,transforms.shape[2]*2))

	for i in xrange(transforms.shape[2]):
		p_new = np.dot(transforms[:,:,i],p)
		V[:,:,2*i] = np.reshape(p_new[0,:],(width,height))
		V[:,:,2*i+1] = np.reshape(p_new[1,:],(width,height))

	return V
'''

'''
def makeVelocityHeatmap(height,width,joints0,joints1,sigma):

	sigma = (2*sigma)**2

	H = np.zeros((height,width,2))

	xv,yv = np.meshgrid(np.array(range(width)),np.array(range(height)),
						sparse=False,indexing='xy')
	H[:,:,0] = xv
	H[:,:,1] = yv	

	for i in xrange(joints1.shape[0]):
		if(joints1[i,0] <= 0 or joints1[i,1] <= 0  or joints1[i,0] >= width-1 or
			joints1[i,1] >= height-1):
			continue	

		H[:,:,0] += makeGaussianMap(width,height,joints1[i,:],sigma,sigma,0.0) * (joints0[i,0]-joints1[i,0])
		H[:,:,1] += makeGaussianMap(width,height,joints1[i,:],sigma,sigma,0.0) * (joints0[i,1]-joints1[i,1])

	return H
'''

def makeJointHeatmaps(height,width,joints,sigma,pose_dn):

	height = height/pose_dn
	width = width/pose_dn
	sigma = sigma**2
	joints = joints/pose_dn

	H = np.zeros((height,width,14)) #len(limbs)-1))

	for i in xrange(H.shape[2]):
		if(joints[i,0] <= 0 or joints[i,1] <= 0  or joints[i,0] >= width-1 or
			joints[i,1] >= height-1):
			continue
	
		H[:,:,i] = makeGaussianMap(width,height,joints[i,:],sigma,sigma,0.0)

	return H

def makeGaussianMap(img_width,img_height,center,sigma_x,sigma_y,theta):

	xv,yv = np.meshgrid(np.array(range(img_width)),np.array(range(img_height)),
						sparse=False,indexing='xy')
	
	a = np.cos(theta)**2/(2*sigma_x) + np.sin(theta)**2/(2*sigma_y)
	b = -np.sin(2*theta)/(4*sigma_x) + np.sin(2*theta)/(4*sigma_y)
	c = np.sin(theta)**2/(2*sigma_x) + np.cos(theta)**2/(2*sigma_y)

	return np.exp(-(a*(xv-center[0])*(xv-center[0]) + 
			2*b*(xv-center[0])*(yv-center[1]) + 
			c*(yv-center[1])*(yv-center[1])))


def makeLimbMasks(joints,img_width,img_height):

	n_limbs = len(limbs)
	n_joints = joints.shape[0]

	mask = np.zeros((img_height,img_width,n_limbs))

	#Gaussian sigma perpendicular to the limb axis. I hardcoded
	#reasonable sigmas for now.
	sigma_perp = np.array([11,11,11,11,11,11,11,11,11,13])**2	 

	for i in xrange(n_limbs):	
		n_joints_for_limb = len(limbs[i])
		p = np.zeros((n_joints_for_limb,2))

		for j in xrange(n_joints_for_limb):
			p[j,:] = [joints[limbs[i][j],0],joints[limbs[i][j],1]]

		if(n_joints_for_limb == 4):
			p_top = np.mean(p[0:2,:],axis=0)
			p_bot = np.mean(p[2:4,:],axis=0)
			p = np.vstack((p_top,p_bot))

		center = np.mean(p,axis=0)		

		sigma_parallel = np.max([5,(np.sum((p[1,:] - p[0,:])**2))/1.5])
		theta = np.arctan2(p[1,1] - p[0,1], p[0,0] - p[1,0])

		mask_i = makeGaussianMap(img_width,img_height,center,sigma_parallel,sigma_perp[i],theta)
		mask[:,:,i] = mask_i/(np.amax(mask_i) + 1e-6)
		
	return mask


def getLimbTransforms(joints1,joints2): 
	
	n_limbs = len(limbs)
	n_joints = joints1.shape[0]

	Ms = np.zeros((2,3,10))
	ctr = 0
	for i in xrange(n_limbs):	

		n_joints_for_limb = len(limbs[i])
		p1 = np.zeros((n_joints_for_limb,2))
		p2 = np.zeros((n_joints_for_limb,2))

		for j in xrange(n_joints_for_limb):
			p1[j,:] = [joints1[limbs[i][j],0],joints1[limbs[i][j],1]]
			p2[j,:] = [joints2[limbs[i][j],0],joints2[limbs[i][j],1]]			

		tform = transformations.make_similarity(p2,p1,False)
		Ms[:,:,ctr] = np.array([[tform[1],-tform[3],tform[0]],[tform[3],tform[1],tform[2]]])
		ctr+=1

		'''
		if(i >= 1 and i <= 4):
			tform = transformations.make_similarity(p2,p1,True)	
			Ms[:,:,ctr] = np.array([[tform[1],tform[3],tform[0]],[tform[3],-tform[1],tform[2]]])
			ctr+=1
		'''
		
		#M = np.array([[tform[1], tform[2], tform[0]],[tform[5],tform[6],tform[4]]])
		#Ms[:,:,i] = M

	return Ms
